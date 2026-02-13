#!/usr/bin/env python3
"""
AI Survey Moderator - Cost Calculator

This script helps you estimate costs for running surveys using different approaches:
1. LiveKit Direct (Recommended - Cheapest)
2. Recall.ai + Zoom Integration
3. Custom Zoom SDK (Future)

Usage:
    python3 cost_calculator.py

Or with custom parameters:
    python3 cost_calculator.py --participants 50 --duration 45 --surveys-per-month 20
"""

import argparse
from typing import Dict, Tuple


class CostCalculator:
    """Calculate costs for different AI survey moderator approaches"""

    # Pricing (as of 2024, subject to change - check provider websites)
    PRICING = {
        'livekit': {
            'free_tier': {
                'egress_minutes': 10000,  # 10k minutes/month free
                'participants': float('inf'),  # No participant limit
                'description': 'Free for development'
            },
            'paid': {
                'per_participant_minute': 0.0008,  # $0.0008 per participant-minute
                'description': 'Production pricing'
            }
        },
        'openai': {
            'whisper_stt': 0.006,  # $0.006 per minute (STT)
            'gpt4o_mini': {
                'input': 0.150 / 1_000_000,   # $0.15 per 1M input tokens
                'output': 0.600 / 1_000_000,  # $0.60 per 1M output tokens
                'avg_tokens_per_participant': 5000,  # Estimated (varies by survey)
            },
            'tts': 15.00 / 1_000_000,  # $15 per 1M characters
            'avg_chars_per_survey': 2000,  # Estimated agent speech
        },
        'recall': {
            'per_minute': 0.03,  # $0.03 per minute per participant
            'storage_per_gb_month': 0.10,  # $0.10 per GB/month
            'avg_mb_per_hour': 50,  # ~50MB per hour of recording
        },
        'zoom': {
            'basic': 0,  # Free
            'pro': 14.99,  # $14.99/month/host
            'business': 19.99,  # $19.99/month/host
        }
    }

    def __init__(
        self,
        num_participants: int = 10,
        duration_minutes: int = 30,
        surveys_per_month: int = 10,
        retention_days: int = 7
    ):
        """
        Initialize cost calculator.

        Args:
            num_participants: Number of participants per survey
            duration_minutes: Duration of each survey in minutes
            surveys_per_month: Number of surveys conducted per month
            retention_days: Days to retain Recall.ai recordings
        """
        self.num_participants = num_participants
        self.duration_minutes = duration_minutes
        self.surveys_per_month = surveys_per_month
        self.retention_days = retention_days

    def calculate_livekit_direct(self) -> Dict[str, float]:
        """
        Calculate costs for LiveKit Direct approach (no Zoom/Recall).

        This is the RECOMMENDED approach - cheapest and most privacy-friendly.
        """
        # LiveKit costs
        total_participant_minutes = (
            self.num_participants *
            self.duration_minutes *
            self.surveys_per_month
        )

        # Check if within free tier
        free_minutes = self.PRICING['livekit']['free_tier']['egress_minutes']

        if total_participant_minutes <= free_minutes:
            livekit_cost = 0
            is_free_tier = True
        else:
            # Only pay for minutes beyond free tier
            billable_minutes = total_participant_minutes - free_minutes
            livekit_cost = billable_minutes * self.PRICING['livekit']['paid']['per_participant_minute']
            is_free_tier = False

        # OpenAI costs
        openai_cost = self._calculate_openai_costs()

        total_cost = livekit_cost + openai_cost
        cost_per_survey = total_cost / self.surveys_per_month if self.surveys_per_month > 0 else 0
        cost_per_participant = cost_per_survey / self.num_participants if self.num_participants > 0 else 0

        return {
            'livekit': livekit_cost,
            'openai': openai_cost,
            'total_monthly': total_cost,
            'per_survey': cost_per_survey,
            'per_participant': cost_per_participant,
            'is_free_tier': is_free_tier,
            'participant_minutes': total_participant_minutes,
        }

    def calculate_recall_zoom(self) -> Dict[str, float]:
        """
        Calculate costs for Recall.ai + Zoom integration.

        More expensive but allows using existing Zoom infrastructure.
        """
        # Recall.ai costs (per-minute charges)
        recall_minutes = (
            self.num_participants *
            self.duration_minutes *
            self.surveys_per_month
        )
        recall_cost = recall_minutes * self.PRICING['recall']['per_minute']

        # Storage costs (recordings retained for N days)
        avg_recording_size_gb = (
            self.PRICING['recall']['avg_mb_per_hour'] *
            (self.duration_minutes / 60) *
            self.num_participants / 1024
        )

        # Average storage cost (recordings are created and deleted over the month)
        # Simplified: assume average of half the retention period
        avg_retention_months = (self.retention_days / 2) / 30
        storage_cost = (
            avg_recording_size_gb *
            self.surveys_per_month *
            avg_retention_months *
            self.PRICING['recall']['storage_per_gb_month']
        )

        # OpenAI costs (same as LiveKit direct)
        openai_cost = self._calculate_openai_costs()

        # Zoom costs (if not already subscribed)
        # Assume using Zoom Pro for professional surveys
        zoom_cost = self.PRICING['zoom']['pro']

        total_cost = recall_cost + storage_cost + openai_cost + zoom_cost
        cost_per_survey = total_cost / self.surveys_per_month if self.surveys_per_month > 0 else 0
        cost_per_participant = cost_per_survey / self.num_participants if self.num_participants > 0 else 0

        return {
            'recall_minutes': recall_cost,
            'recall_storage': storage_cost,
            'openai': openai_cost,
            'zoom_subscription': zoom_cost,
            'total_monthly': total_cost,
            'per_survey': cost_per_survey,
            'per_participant': cost_per_participant,
            'participant_minutes': recall_minutes,
        }

    def _calculate_openai_costs(self) -> float:
        """Calculate OpenAI costs (STT, LLM, TTS)"""
        total_surveys = self.surveys_per_month

        # Speech-to-Text (Whisper)
        # Only transcribe participants, not agent (agent uses TTS)
        stt_minutes = self.num_participants * self.duration_minutes * total_surveys
        stt_cost = stt_minutes * self.PRICING['openai']['whisper_stt']

        # LLM (GPT-4o-mini)
        tokens_per_survey = (
            self.PRICING['openai']['gpt4o_mini']['avg_tokens_per_participant'] *
            self.num_participants
        )
        # Assume 60% input, 40% output token ratio
        input_tokens = tokens_per_survey * 0.6 * total_surveys
        output_tokens = tokens_per_survey * 0.4 * total_surveys

        llm_cost = (
            input_tokens * self.PRICING['openai']['gpt4o_mini']['input'] +
            output_tokens * self.PRICING['openai']['gpt4o_mini']['output']
        )

        # Text-to-Speech
        chars_total = self.PRICING['openai']['avg_chars_per_survey'] * total_surveys
        tts_cost = chars_total * self.PRICING['openai']['tts']

        return stt_cost + llm_cost + tts_cost

    def print_comparison(self):
        """Print detailed cost comparison"""
        livekit_costs = self.calculate_livekit_direct()
        recall_costs = self.calculate_recall_zoom()

        print("\n" + "="*80)
        print("AI SURVEY MODERATOR - COST COMPARISON")
        print("="*80)
        print(f"\n📊 Survey Parameters:")
        print(f"   • Participants per survey: {self.num_participants}")
        print(f"   • Duration per survey: {self.duration_minutes} minutes")
        print(f"   • Surveys per month: {self.surveys_per_month}")
        print(f"   • Total participant-minutes/month: {livekit_costs['participant_minutes']:,}")

        # LiveKit Direct
        print("\n" + "="*80)
        print("APPROACH 1: LiveKit Direct (RECOMMENDED)")
        print("="*80)
        print(f"✅ No Zoom/Recall.ai needed - participants join via browser link")
        print(f"✅ Full privacy - no 3rd party recording storage")
        print(f"✅ Lowest cost\n")

        if livekit_costs['is_free_tier']:
            print(f"🎉 FREE TIER! You're within LiveKit's free 10,000 minutes/month")

        print(f"LiveKit:")
        print(f"   Infrastructure: ${livekit_costs['livekit']:>8.2f}/month")
        print(f"\nOpenAI (STT/LLM/TTS):")
        print(f"   AI Processing: ${livekit_costs['openai']:>8.2f}/month")
        print(f"\n{'─'*80}")
        print(f"Total Monthly Cost: ${livekit_costs['total_monthly']:>8.2f}")
        print(f"Cost per Survey:    ${livekit_costs['per_survey']:>8.2f}")
        print(f"Cost per Participant: ${livekit_costs['per_participant']:>8.2f}")

        # Recall + Zoom
        print("\n" + "="*80)
        print("APPROACH 2: Recall.ai + Zoom")
        print("="*80)
        print(f"⚠️  Requires existing Zoom infrastructure")
        print(f"⚠️  Recordings stored on Recall.ai servers (retention: {self.retention_days} days)")
        print(f"⚠️  Per-minute charges add up quickly\n")

        print(f"Recall.ai:")
        print(f"   Per-minute charges: ${recall_costs['recall_minutes']:>8.2f}/month")
        print(f"   Storage ({self.retention_days} days): ${recall_costs['recall_storage']:>8.2f}/month")
        print(f"\nZoom:")
        print(f"   Pro Subscription: ${recall_costs['zoom_subscription']:>8.2f}/month")
        print(f"\nOpenAI (STT/LLM/TTS):")
        print(f"   AI Processing: ${recall_costs['openai']:>8.2f}/month")
        print(f"\n{'─'*80}")
        print(f"Total Monthly Cost: ${recall_costs['total_monthly']:>8.2f}")
        print(f"Cost per Survey:    ${recall_costs['per_survey']:>8.2f}")
        print(f"Cost per Participant: ${recall_costs['per_participant']:>8.2f}")

        # Savings comparison
        print("\n" + "="*80)
        print("💰 SAVINGS ANALYSIS")
        print("="*80)

        monthly_savings = recall_costs['total_monthly'] - livekit_costs['total_monthly']
        if monthly_savings > 0:
            savings_pct = (monthly_savings / recall_costs['total_monthly']) * 100
            annual_savings = monthly_savings * 12

            print(f"LiveKit Direct saves you:")
            print(f"   • ${monthly_savings:.2f}/month ({savings_pct:.1f}% cheaper)")
            print(f"   • ${annual_savings:.2f}/year")
            print(f"\n✅ LiveKit Direct is {savings_pct:.0f}% cheaper!")
        else:
            print("Both approaches have similar costs for your usage.")

        # Break-even analysis
        print("\n" + "="*80)
        print("📈 SCALE ANALYSIS")
        print("="*80)

        # Calculate cost at different scales
        scales = [5, 10, 25, 50, 100]
        print(f"\n{'Surveys/Month':<15} {'LiveKit Direct':<20} {'Recall + Zoom':<20} {'Savings'}")
        print("─" * 80)

        for scale in scales:
            temp_calc = CostCalculator(
                self.num_participants,
                self.duration_minutes,
                scale,
                self.retention_days
            )
            lk_cost = temp_calc.calculate_livekit_direct()['total_monthly']
            rc_cost = temp_calc.calculate_recall_zoom()['total_monthly']
            savings = rc_cost - lk_cost

            print(f"{scale:<15} ${lk_cost:<19.2f} ${rc_cost:<19.2f} ${savings:.2f}")

        # Recommendations
        print("\n" + "="*80)
        print("💡 RECOMMENDATIONS")
        print("="*80)

        if livekit_costs['is_free_tier']:
            print("\n✅ USE LIVEKIT DIRECT - You're in the FREE tier!")
            print("   • No costs for infrastructure")
            print("   • Only pay for OpenAI (STT/LLM/TTS)")
            print("   • Run: python3 join_survey_custom_names.py")
        else:
            print("\n✅ USE LIVEKIT DIRECT - Significantly cheaper!")
            print(f"   • Save ${monthly_savings:.2f}/month")
            print("   • Better privacy (no 3rd party storage)")
            print("   • Run: python3 join_survey_custom_names.py")

        print("\n⚠️  ONLY USE RECALL.AI + ZOOM IF:")
        print("   • You already have Zoom infrastructure")
        print("   • Participants MUST join via Zoom (company policy)")
        print("   • You need Zoom's advanced features (waiting rooms, etc.)")
        print("   • Budget allows for higher costs")

        print("\n" + "="*80)
        print("📚 NEXT STEPS")
        print("="*80)
        print("\n1. Try LiveKit Direct (free):")
        print("   Terminal 1: python3 agent.py dev")
        print("   Terminal 2: python3 join_survey_custom_names.py --participants", self.num_participants)
        print("\n2. If you need Zoom integration:")
        print("   Terminal 1: python3 agent.py dev")
        print("   Terminal 2: python3 zoom_survey.py")
        print("\n3. Re-run this calculator with your actual usage:")
        print(f"   python3 cost_calculator.py --participants {self.num_participants} --duration {self.duration_minutes} --surveys-per-month {self.surveys_per_month}")
        print()


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Calculate costs for AI Survey Moderator',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Default calculation (10 participants, 30 min, 10 surveys/month)
    python3 cost_calculator.py

    # Large organization (50 participants, 45 min, 100 surveys/month)
    python3 cost_calculator.py --participants 50 --duration 45 --surveys-per-month 100

    # Small team (5 participants, 20 min, 5 surveys/month)
    python3 cost_calculator.py --participants 5 --duration 20 --surveys-per-month 5

    # With custom retention period
    python3 cost_calculator.py --participants 20 --duration 30 --surveys-per-month 15 --retention-days 1
        """
    )

    parser.add_argument(
        '--participants',
        type=int,
        default=10,
        help='Number of participants per survey (default: 10)'
    )

    parser.add_argument(
        '--duration',
        type=int,
        default=30,
        help='Duration of each survey in minutes (default: 30)'
    )

    parser.add_argument(
        '--surveys-per-month',
        type=int,
        default=10,
        help='Number of surveys per month (default: 10)'
    )

    parser.add_argument(
        '--retention-days',
        type=int,
        default=7,
        help='Days to retain Recall.ai recordings (default: 7)'
    )

    args = parser.parse_args()

    # Validate inputs
    if args.participants < 1 or args.participants > 1000:
        print("❌ Error: Participants must be between 1 and 1000")
        return

    if args.duration < 1 or args.duration > 480:
        print("❌ Error: Duration must be between 1 and 480 minutes (8 hours)")
        return

    if args.surveys_per_month < 1 or args.surveys_per_month > 1000:
        print("❌ Error: Surveys per month must be between 1 and 1000")
        return

    if args.retention_days < 1 or args.retention_days > 365:
        print("❌ Error: Retention days must be between 1 and 365")
        return

    # Create calculator and print comparison
    calculator = CostCalculator(
        num_participants=args.participants,
        duration_minutes=args.duration,
        surveys_per_month=args.surveys_per_month,
        retention_days=args.retention_days
    )

    calculator.print_comparison()


if __name__ == '__main__':
    main()
